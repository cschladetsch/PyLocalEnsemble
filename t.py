class Foo:
    def __init__(self,x,y):
        self.x = x
        self.y = y
        
    def add(self, z) -> int:
        return self.x + self.y + z

def main():
    foo = Foo(1,2)
    r = foo.add(2)
    assert(r ==5)

main()

